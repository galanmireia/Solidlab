package DIP;

import java.util.Map;

public class main {

	public static void main(String[] args) {
		// TODO Auto-generated method stub
		
		
		Covid19Pacient mireia = new Covid19Pacient("Mireia",22);
		
		NeuroMuscularSymptom sintoma1 = new NeuroMuscularSymptom(2, 30, "sintoma1",2.0 );
		
		CardioVascularSymptom sintoma2 = new CardioVascularSymptom(3, 20, "sintoma2", 3.0);
		
		RespiratorySymptom sintoma3 = new RespiratorySymptom(4,10,"sintoma3", 4.5);
		
	
		mireia.addSymptom(sintoma1,1);
		mireia.addSymptom(sintoma2,2);
		mireia.addSymptom(sintoma3,3);
		
		Impact inpt = new Impact();
		Increment incr = new Increment();
		Afection ca = new Afection();
		
		System.out.println( mireia.calcCovid19Impact(ca, incr, inpt, mireia.symptoms));
		
		System.out.println(mireia.sanatedDays());
	
		
		
		

	}

}
