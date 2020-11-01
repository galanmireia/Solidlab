package SRP;

public class CalcIncrement {

	
	
	public  double calculateIncrementPerYear( double afection,double years) {
		double increment=0;
		
		if(years> 45 && years < 65) {
			increment = afection * 0.3;
		}
		if (years > 65)
			increment = afection * 0.5;
		// calculate impact
	
		return increment;
	}
}
